# Hardware notes

Snapshot from 2026-11-08, verified on the real board. Re-check with `lsusb`
and `cat /sys/firmware/devicetree/base/compatible` before relying on it.

## Tested setup

- Host: Arduino **VENTUNO Q** (`arduino,monza`, Linux/aarch64).
- Reader: **ACS ACR1552** (`072f:2303`), firmware `ACR1552 R FW 1.06.02`,
  factory polling type `07 05`. nfc-cli talks raw USB CCID (extra `usb`,
  pyusb); no `pcscd` is needed.
- Xteink X3 NFC tag: passive MIFARE Classic 1K, UID `cb30c560`, factory
  blank, keys `FFFFFFFFFFFF`. Block 0 manufacturer bytes read `bcdefghi`,
  which is a clone-chip signature (magic-style blank). It reads as
  `family: classic_1k`.
- The transparent sticker supplied with it is the same chip family (UID
  `3d100a1e`).

## Reader access: uaccess and the seat caveat

On this board the reader is granted to the logged-in user by
systemd-logind through the udev `uaccess` tag (the device carries
`:security-device:seat:systemd:uaccess:`). That works only while the user
has an **active local seat session** (a console or desktop login).
Headless, SSH or service use has no such session, so the device node is not
accessible and nfc-cli reports an environment error (exit 2).

For those cases install the supplied rule, which grants `072f:2303` to
group `plugdev` with mode `0660`:

```bash
sudo cp docs/udev/60-nfc-cli-acr1552.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo usermod -aG plugdev $USER
# then unplug and replug the reader, and log in again for the group change
```

## What the reader cannot see

The ACR1552 is a 13.56 MHz reader. Tags on other frequencies are invisible
to it, not broken. A card and a "spot" sticker tried in testing produced no
response; they are most likely 125 kHz (LF) or UHF tags. Use a matching
reader for those.

## Weak RF coupling (Xteink X3)

The X3 tag couples weakly. Finding a stable position took about 18 seconds
of repositioning in testing.

- Hold the device flat and completely still on the reader.
- Use `--wait SECONDS` (for example `nfc-cli tag info --wait 30`) so the
  command keeps polling while you adjust.
- Read first (`tag dump`) and only then write; a tag lost mid-write is
  resumable with `tag restore --resume JOURNAL`.
- iPhones read the X3's Classic tag unreliably (iOS has no MIFARE Classic
  support). Android works.
