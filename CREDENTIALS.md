# Credentials

Private repository: everyone with access to it can log in to the Pi and join SWARM-NET.
When a password changes, update this file and commit.

| What | Value |
|---|---|
| Pi device / hostname | `Swarm-Pi` (mDNS `swarm-pi.local`) |
| Pi user | `swarm-pi` (lowercase) |
| Pi password | `12345` |
| SWARM-NET WiFi passphrase | `SwarmPi1` (also compiled into the deck station image via `wifi_credentials.h`) |
| Last changed | 2026-11-10 (set during bring-up) |

Preferred login (plain `ssh` misbehaves without the option):
```powershell
ssh -o PreferredAuthentications=password swarm-pi@swarm-pi.local
```
If `swarm-pi.local` does not answer, use the direct address on SWARM-NET:
```powershell
ssh -o PreferredAuthentications=password swarm-pi@192.168.50.1
```
All connection options and fixes: [docs/pi-access.md](docs/pi-access.md).
