#!/usr/bin/env bash
# One-time setup on an Ubuntu/Debian laptop. Run from anywhere:
#     bash flight/setup_linux.sh
# It asks for your Linux password (sudo) for the system parts.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
echo "== Repository: $REPO"

echo "== 1/4 System packages (Python venv, OpenCV runtime libraries)"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip libgl1 libglib2.0-0

echo "== 2/4 Python environment in $REPO/.venv"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install cflib numpy pyyaml opencv-python

echo "== 3/4 Crazyradio USB permission (Bitcraze udev rules)"
sudo groupadd -f plugdev
sudo usermod -aG plugdev "$USER"
sudo tee /etc/udev/rules.d/99-bitcraze.rules > /dev/null <<'RULES'
# Crazyradio (normal operation)
SUBSYSTEM=="usb", ATTRS{idVendor}=="1915", ATTRS{idProduct}=="7777", MODE="0664", GROUP="plugdev"
# Crazyradio bootloader
SUBSYSTEM=="usb", ATTRS{idVendor}=="1915", ATTRS{idProduct}=="0101", MODE="0664", GROUP="plugdev"
# Crazyflie over USB
SUBSYSTEM=="usb", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5740", MODE="0664", GROUP="plugdev"
RULES
sudo udevadm control --reload-rules
sudo udevadm trigger

echo "== 4/4 Check"
python -m unittest tests.test_swarm
python -c "import cflib, cv2, numpy, yaml; print('cflib, OpenCV, numpy, yaml: OK')"

echo
echo "Done. Now LOG OUT and back in (or reboot) so the Crazyradio permission applies,"
echo "then unplug and replug the Crazyradio. Every new terminal: cd \"$REPO\" && source .venv/bin/activate"
