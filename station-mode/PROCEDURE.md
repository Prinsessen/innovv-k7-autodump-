> **Historical (night 1).** The camera's rootfs turned out to be read-only and full, so the `/etc/wifi` + `/etc/init.d` steps below do not work. Current scripts: `scripts/wifi_sta_stay.sh` and `scripts/sta_diag.sh`; see README.md.

# INNOV K7 — få kameraet til at joine eksisterende WiFi (station mode)

Mål: kameraet forbinder selv til SSID **HOME_SSID** i stedet for kun at lave sit eget AP.
Metode: læg programmet `wpa_supplicant` (som mangler i firmwaren) + config ind via telnet.
Ingen firmware flashes — alt lægges på den skrivbare overlay-partition, så det kan rulles tilbage.

Filer i denne mappe:
- `wpa_supplicant`         — den byggede ARM-binær (kommer når brew er færdig)
- `wpa_supplicant.conf`    — netværkskonfig (SSID HOME_SSID)
- `wifi_sta.sh`            — bringer wlan0 op som station (kør manuelt / fra boot)
- `S99_wifi_sta`           — boot-script der auto-starter station mode
- `PROCEDURE.md`           — denne vejledning

---

## 0. Forberedelse (på Mac'en)
1. Behold en kopi af den originale **in-k7-1.bin** et sikkert sted (ultimativ nulstilling).
2. Læg disse filer på et FAT32 SD-kort (kameraets kort):
   - `wpa_supplicant`, `wpa_supplicant.conf`, `wifi_sta.sh`, `S99_wifi_sta`
3. Sæt kortet i kameraet. Tænd det. Forbind Mac'en til kameraets WiFi-AP
   (det du normalt forbinder telefonen til).

## 1. Log ind på kameraet
```bash
telnet 192.168.1.254
```
Login: `root`, intet kodeord.  (Virker ikke IP'en, så find gatewayen med `ifconfig` på Mac'en,
eller prøv 192.168.1.1 / 192.168.1.3.)

## 2. BACKUP — kør dette FØRST, før noget ændres
SD-kortet sidder typisk som `/mnt/sd`.
```sh
cat /proc/mtd                                   # se partitioner
tar czf /mnt/sd/etc-backup.tar.gz /etc /var     # backup af det vi kan røre
# Rå flash-snapshot (én linje pr. mtd-nummer fra /proc/mtd):
dd if=/dev/mtdblock0 of=/mnt/sd/backup_mtd0.bin
dd if=/dev/mtdblock1 of=/mnt/sd/backup_mtd1.bin
# ...osv for hver mtdblockN...
sync
```

## 3. Installér filerne (tilføjer kun, overskriver intet)
```sh
mkdir -p /etc/wifi
cp /mnt/sd/wpa_supplicant      /etc/wifi/
cp /mnt/sd/wpa_supplicant.conf /etc/wifi/
cp /mnt/sd/wifi_sta.sh         /etc/wifi/
chmod +x /etc/wifi/wpa_supplicant /etc/wifi/wifi_sta.sh
sync
```

## 4. TEST LIVE (endnu ikke permanent)
```sh
/etc/wifi/wifi_sta.sh
# vent ~10 sek, tjek så:
iwconfig wlan0        # skal vise ESSID:"HOME_SSID" og en Access Point MAC
ifconfig wlan0        # skal vise en inet-adresse fra din router
ping -c 3 8.8.8.8     # internet gennem dit net
```
- Virker det: fortsæt til trin 5.
- Virker det ikke: se "Fejlfinding" nederst. Intet er permanent endnu —
  en genstart bringer kameraet tilbage til normal AP-tilstand.

## 5. Gør det permanent (auto ved boot)
```sh
cp /mnt/sd/S99_wifi_sta /etc/init.d/S99_wifi_sta
chmod +x /etc/init.d/S99_wifi_sta
sync
reboot
```
Efter genstart joiner kameraet **HOME_SSID** automatisk (~15 sek inde i boot).
Du kan stadig telnette ind — over dit net (kameraets nye IP fra routeren) eller via Ethernet.

---

## FORTRYD / ROLLBACK

### A. Fjern kun station-mode (tilbage til stock-opførsel)
```sh
killall wpa_supplicant 2>/dev/null
rm -f /etc/init.d/S99_wifi_sta
rm -rf /etc/wifi
sync
reboot
```
Kameraet er nu præcis som før (AP-mode). Intet firmware blev ændret.

### B. Hvis en eksisterende fil blev ændret
Gendan fra backuppen, fx:
```sh
cp /mnt/sd/S25_Net.orig /etc/init.d/S25_Net
sync; reboot
```
Eller gendan hele /etc:
```sh
cd / && tar xzf /mnt/sd/etc-backup.tar.gz
sync; reboot
```

### C. Fuld nulstilling (ultimativ)
Flash den originale **in-k7-1.bin** via SD-kortet (kameraets normale opdaterings-/recovery-flow).
Det sætter alt tilbage til fabriksstand og tørrer overlay'et.

---

## Fejlfinding
- `wifi_sta.sh` kan ikke finde wpa_supplicant: tjek `ls -l /etc/wifi/` og `chmod +x`.
- Associerer ikke med wext: ret i `wifi_sta.sh` `-Dwext` til `-Dnl80211` og prøv igen.
- Får ikke IP: kør `udhcpc -i wlan0` manuelt og se output.
- cardv tager wlan0 tilbage (AP kommer op igen): så skal cardv's AP-opstart dæmpes —
  det gør vi live sammen; husk `cp /etc/init.d/S25_Net /mnt/sd/S25_Net.orig` før ændring.
- Mistet al adgang: tilslut Ethernet (telnetd kører også på eth0), ellers flash original bin.
