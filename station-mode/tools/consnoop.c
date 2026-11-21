/*
 * consnoop — capture everything user space writes to /dev/console
 * (e.g. cardv's printf output, which normally only goes to the serial port)
 * and append it, time-stamped, to a file on the SD card.
 *
 * How: open a pseudo-terminal pair, then ioctl(TIOCCONS) on the slave side.
 * The kernel then redirects writes to /dev/console into that pty; we read the
 * master side. Kernel printk output is NOT affected (it still goes to ttyS0).
 * The redirect is released when this process exits (or on reboot).
 *
 * Build (macOS cross toolchain):
 *   armv7-unknown-linux-musleabihf-gcc -Os -static -o consnoop consnoop.c
 * Run on camera:  ./consnoop /mnt/sd/console.log &
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

static void stamp(int fd) {
    char ts[40];
    time_t now = time(NULL);
    struct tm tmv;
    localtime_r(&now, &tmv);
    int n = strftime(ts, sizeof ts, "\n[%H:%M:%S] ", &tmv);
    if (n > 0) write(fd, ts, n);
}

int main(int argc, char **argv) {
    const char *out = argc > 1 ? argv[1] : "/mnt/sd/console.log";
    int m = posix_openpt(O_RDWR | O_NOCTTY);
    if (m < 0) { perror("posix_openpt"); return 1; }
    if (grantpt(m) < 0 || unlockpt(m) < 0) { perror("grant/unlock"); return 1; }
    char *sn = ptsname(m);
    int s = open(sn, O_RDWR | O_NOCTTY);
    if (s < 0) { perror("open slave"); return 1; }

    struct termios t;
    if (tcgetattr(s, &t) == 0) { cfmakeraw(&t); tcsetattr(s, TCSANOW, &t); }

    if (ioctl(s, TIOCCONS, NULL) < 0) { perror("TIOCCONS"); return 2; }

    int fd = open(out, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0) { perror("open out"); return 3; }
    const char *hdr = "\n===== consnoop started =====";
    write(fd, hdr, strlen(hdr));
    stamp(fd);
    fsync(fd);

    char buf[4096];
    int at_line_start = 0;
    for (;;) {
        ssize_t n = read(m, buf, sizeof buf);
        if (n <= 0) { usleep(200000); continue; }
        for (ssize_t i = 0; i < n; i++) {
            if (at_line_start) { stamp(fd); at_line_start = 0; }
            if (buf[i] == '\n') { at_line_start = 1; continue; }
            if (buf[i] == '\r') continue;
            write(fd, &buf[i], 1);
        }
        fsync(fd);
    }
    return 0;
}
