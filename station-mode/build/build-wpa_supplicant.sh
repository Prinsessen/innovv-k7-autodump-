#!/bin/bash
# Build a SELF-CONTAINED (static) wpa_supplicant for the INNOV K7 camera.
#
# Target CPU : ARM Cortex-A9 (ARMv7), 32-bit.  Camera uClibc is irrelevant
#              because we link static + use wpa_supplicant's INTERNAL crypto,
#              so the binary carries everything it needs.
# Driver     : wext only  -> NO libnl needed -> trivial static build.
#
# WHERE TO RUN THIS:
#  * 32-bit Raspberry Pi OS (armhf, Pi 2/3/4 in 32-bit, or Pi Zero/1):
#        run it directly -> native armv6/armv7 hardfloat binary (runs on Cortex-A9).
#  * 64-bit Raspberry Pi OS (arm64) or any Linux PC:
#        install a 32-bit ARM cross compiler and set CROSS/CC (see below).
#
# Output: ./wpa_supplicant  (copy this to the camera as /etc/wifi/wpa_supplicant)

set -e
VER=2.10
URL="https://w1.fi/releases/wpa_supplicant-${VER}.tar.gz"

# --- toolchain -------------------------------------------------------------
# NATIVE build on a 32-bit Pi: leave CC unset (uses system gcc).
# CROSS build (64-bit Pi / x86 Linux): install the cross gcc and uncomment:
#   sudo apt-get install -y gcc-arm-linux-gnueabihf
#   export CC=arm-linux-gnueabihf-gcc
CC="${CC:-gcc}"

echo "== deps (Debian/RaspberryPiOS): build tools + openssl headers not needed (internal TLS)"
sudo apt-get update
sudo apt-get install -y build-essential wget

echo "== fetch wpa_supplicant ${VER}"
rm -rf "wpa_supplicant-${VER}"
wget -O "wpa_supplicant-${VER}.tar.gz" "$URL"
tar xf "wpa_supplicant-${VER}.tar.gz"
cd "wpa_supplicant-${VER}/wpa_supplicant"

echo "== write .config (wext driver, internal crypto/TLS, fully static)"
cat > .config <<'CFG'
CONFIG_DRIVER_WEXT=y
CONFIG_DRIVER_NL80211=n
CONFIG_CTRL_IFACE=y
CONFIG_BACKEND=file
CONFIG_WPS=n
CONFIG_IEEE8021X_EAPOL=n
# Use wpa_supplicant's own crypto so we do NOT need OpenSSL/libnl:
CONFIG_TLS=internal
CONFIG_INTERNAL_LIBTOMMATH=y
CONFIG_CRYPTO_INTERNAL=y
# Static, self-contained binary:
CFLAGS += -Os -ffunction-sections -fdata-sections
LDFLAGS += -static -Wl,--gc-sections
CFG

echo "== build"
make clean >/dev/null 2>&1 || true
make CC="$CC" -j"$(nproc)" wpa_supplicant

echo "== verify it is ARM + static"
file ./wpa_supplicant
cp -f ./wpa_supplicant ../../wpa_supplicant

echo
echo "DONE -> $(pwd)/../../wpa_supplicant"
echo "Expect: 'ELF 32-bit LSB executable, ARM ... statically linked'"
echo "Copy that file to the camera as /etc/wifi/wpa_supplicant"
