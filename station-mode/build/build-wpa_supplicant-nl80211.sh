#!/bin/bash
# Build a STATIC wpa_supplicant 2.10 with nl80211 (+ wext fallback) for the INNOV K7
# (ARMv7 Cortex-A9). Built on macOS with the Homebrew cross toolchain
#   brew install messense/macos-cross-toolchains/armv7-unknown-linux-musleabihf
# Why nl80211: the rtl8821cs v5.x driver rejects wext SIOCSIWAP and never emits the
# wext association event, so a wext-only wpa_supplicant can't complete association.
set -e
CC=armv7-unknown-linux-musleabihf-gcc
AR=armv7-unknown-linux-musleabihf-ar
RANLIB=armv7-unknown-linux-musleabihf-ranlib
WORK="${WORK:-$PWD/nl-build}"
PREFIX="$WORK/sysroot"
mkdir -p "$WORK" "$PREFIX/lib" "$PREFIX/include/libnl3"
cd "$WORK"

# --- 1. libnl 3.9.0 (only libnl-3 + libnl-genl-3, static) -------------------
curl -fsSL -o libnl.tar.gz https://github.com/thom311/libnl/releases/download/libnl3_9_0/libnl-3.9.0.tar.gz
# sha256 aed507004d728a5cf11eab48ca4bf9e6e1874444e33939b9d3dfed25018ee9bb
rm -rf libnl-3.9.0 && tar xf libnl.tar.gz
cd libnl-3.9.0
# GCC 15 defaults to C23 and makes implicit declarations errors -> gnu11 + include time.h
CC=$CC AR=$AR RANLIB=$RANLIB ./configure --host=armv7-unknown-linux-musleabihf --prefix="$PREFIX" \
  --disable-shared --enable-static --disable-cli --disable-debug --disable-pthreads \
  CFLAGS="-Os -march=armv7-a -mtune=cortex-a9 -std=gnu11 -include time.h -Wno-error=implicit-function-declaration"
make -j8 lib/libnl-3.la lib/libnl-genl-3.la     # route/cli parts need bison/flex - not needed
cp lib/.libs/libnl-3.a lib/.libs/libnl-genl-3.a "$PREFIX/lib/"
cp -R include/netlink "$PREFIX/include/libnl3/"
cd "$WORK"

# --- 2. wpa_supplicant 2.10 ----------------------------------------------------
curl -fsSL -o wpa.tar.gz https://w1.fi/releases/wpa_supplicant-2.10.tar.gz
# sha256 20df7ae5154b3830355f8ab4269123a87affdea59fe74fe9292a91d0d7e17b2f
rm -rf wpa_supplicant-2.10 && tar xf wpa.tar.gz
cd wpa_supplicant-2.10/wpa_supplicant
cat > .config <<CFG
CONFIG_DRIVER_NL80211=y
CONFIG_LIBNL32=y
CONFIG_DRIVER_WEXT=y
CONFIG_CTRL_IFACE=y
CONFIG_BACKEND=file
CONFIG_TLS=internal
CONFIG_INTERNAL_LIBTOMMATH=y
CONFIG_CRYPTO_INTERNAL=y
CFLAGS += -Os -march=armv7-a -mtune=cortex-a9 -ffunction-sections -fdata-sections -std=gnu11
LDFLAGS += -static -Wl,--gc-sections
LIBS += -L$PREFIX/lib
CFG
# static link order: genl before nl-3
make CC=$CC LIBNL_INC="$PREFIX/include/libnl3" EXTRALIBS="-lnl-genl-3 -lnl-3" -j8 wpa_supplicant
armv7-unknown-linux-musleabihf-strip wpa_supplicant
file wpa_supplicant   # expect: ELF 32-bit LSB executable, ARM, EABI5, statically linked
cp wpa_supplicant "$WORK/wpa_supplicant_nl80211"
echo "DONE -> $WORK/wpa_supplicant_nl80211   (run on camera with -Dnl80211,wext)"
